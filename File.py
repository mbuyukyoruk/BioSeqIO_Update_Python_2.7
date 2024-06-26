# Copyright 1999 by Jeffrey Chang.  All rights reserved.
# Copyright 2009-2018 by Peter Cock. All rights reserved.
#
# This file is part of the Biopython distribution and governed by your
# choice of the "Biopython License Agreement" or the "BSD 3-Clause License".
# Please see the LICENSE file that should have been included as part of this
# package.
"""Code for more fancy file handles.

Classes:
 - UndoHandle     File object decorator with support for undo-like operations.

Additional private classes used in Bio.SeqIO and Bio.SearchIO for indexing
files are also defined under Bio.File but these are not intended for direct
use.
"""

from __future__ import print_function

import codecs
import os
import sys
import contextlib
import itertools
import platform

from Bio._py3k import basestring

try:
    from collections import UserDict as _dict_base
except ImportError:
    from UserDict import DictMixin as _dict_base

# Ordered dict is a language feature from Python 3.7 onwards.
# CPython 3.6 also already has ordered dicts.
# PyPy has always had ordered dicts.
if (sys.version_info >= (3, 7)
        or platform.python_implementation() == "PyPy"
        or (sys.version_info == (3, 6)
            and platform.python_implementation() == "CPython")):
    _dict = dict
else:
    from collections import OrderedDict as _dict

try:
    from sqlite3 import dbapi2 as _sqlite
    from sqlite3 import IntegrityError as _IntegrityError
    from sqlite3 import OperationalError as _OperationalError
except ImportError:
    # Not present on Jython, but should be included in Python 2.5
    # or later (unless compiled from source without its dependencies)
    # Still want to offer in-memory indexing.
    _sqlite = None
    pass


@contextlib.contextmanager
def as_handle(handleish, mode="r", **kwargs):
    r"""Context manager to ensure we are using a handle.

    Context manager for arguments that can be passed to SeqIO and AlignIO read, write,
    and parse methods: either file objects or path-like objects (strings, pathlib.Path
    instances, or more generally, anything that can be handled by the builtin 'open'
    function).

    When given a path-like object, returns an open file handle to that path, with provided
    mode, which will be closed when the manager exits.

    All other inputs are returned, and are *not* closed.

    Arguments:
     - handleish  - Either a file handle or path-like object (anything which can be
                    passed to the builtin 'open' function: str, bytes, and under
                    Python >= 3.6, pathlib.Path, os.DirEntry)
     - mode       - Mode to open handleish (used only if handleish is a string)
     - kwargs     - Further arguments to pass to open(...)

    Examples
    --------
    >>> from Bio import File
    >>> import os
    >>> with File.as_handle('seqs.fasta', 'w') as fp:
    ...     # Python2/3 docstring workaround, revise for 'Python 3 only'.
    ...     _ = fp.write('>test\nACGT')
    ...
    >>> fp.closed
    True

    >>> handle = open('seqs.fasta', 'w')
    >>> with File.as_handle(handle) as fp:
    ...     # Python 2/3 docstring workaround, revise for 'Python 3 only'.
    ...     _ = fp.write('>test\nACGT')
    ...
    >>> fp.closed
    False
    >>> fp.close()
    >>> os.remove("seqs.fasta")  # tidy up

    Note that if the mode argument includes U (for universal new lines)
    this will be removed under Python 3 where is is redundant and has
    been deprecated (this happens automatically in text mode).

    """
    # If we're running under a version of Python that supports PEP 519, try
    # to convert 'handleish' to a string with 'os.fspath'.
    if hasattr(os, "fspath"):
        try:
            handleish = os.fspath(handleish)
        except TypeError:
            # handleish isn't path-like, and it remains unchanged -- we'll yield it below
            pass

    if isinstance(handleish, basestring):
        if sys.version_info[0] >= 3 and "U" in mode:
            mode = mode.replace("U", "")
        if "encoding" in kwargs:
            with codecs.open(handleish, mode, **kwargs) as fp:
                yield fp
        else:
            with open(handleish, mode, **kwargs) as fp:
                yield fp
    else:
        yield handleish


def _open_for_random_access(filename):
    """Open a file in binary mode, spot if it is BGZF format etc (PRIVATE).

    This functionality is used by the Bio.SeqIO and Bio.SearchIO index
    and index_db functions.

    If the file is gzipped but not BGZF, a specific ValueError is raised.
    """
    handle = open(filename, "rb")
    magic = handle.read(2)
    handle.seek(0)

    if magic == b"\x1f\x8b":
        # This is a gzipped file, but is it BGZF?
        from . import bgzf
        try:
            # If it is BGZF, we support that
            return bgzf.BgzfReader(mode="rb", fileobj=handle)
        except ValueError as e:
            assert "BGZF" in str(e)
            # Not a BGZF file after all,
            handle.close()
            raise ValueError("Gzipped files are not suitable for indexing, "
                             "please use BGZF (blocked gzip format) instead.")

    return handle


class UndoHandle(object):
    """A Python handle that adds functionality for saving lines.

    Saves lines in a LIFO fashion.

    Added methods:
     - saveline    Save a line to be returned next time.
     - peekline    Peek at the next line without consuming it.

    """

    def __init__(self, handle):
        """Initialize the class."""
        self._handle = handle
        self._saved = []
        try:
            # If wrapping an online handle, this this is nice to have:
            self.url = handle.url
        except AttributeError:
            pass

    def __iter__(self):
        """Iterate over the lines in the File."""
        return self

    def __next__(self):
        """Return the next line."""
        next = self.readline()
        if not next:
            raise StopIteration
        return next

    if sys.version_info[0] < 3:
        def next(self):
            """Python 2 style alias for Python 3 style __next__ method."""
            return self.__next__()

    def readlines(self, *args, **keywds):
        """Read all the lines from the file as a list of strings."""
        lines = self._saved + self._handle.readlines(*args, **keywds)
        self._saved = []
        return lines

    def readline(self, *args, **keywds):
        """Read the next line from the file as string."""
        if self._saved:
            line = self._saved.pop(0)
        else:
            line = self._handle.readline(*args, **keywds)
        return line

    def read(self, size=-1):
        """Read the File."""
        if size == -1:
            saved = "".join(self._saved)
            self._saved[:] = []
        else:
            saved = ""
            while size > 0 and self._saved:
                if len(self._saved[0]) <= size:
                    size = size - len(self._saved[0])
                    saved = saved + self._saved.pop(0)
                else:
                    saved = saved + self._saved[0][:size]
                    self._saved[0] = self._saved[0][size:]
                    size = 0
        return saved + self._handle.read(size)

    def saveline(self, line):
        """Store a line in the cache memory for later use.

        This acts to undo a readline, reflecting the name of the class: UndoHandle.
        """
        if line:
            self._saved = [line] + self._saved

    def peekline(self):
        """Return the next line in the file, but do not move forward though the file."""
        if self._saved:
            line = self._saved[0]
        else:
            line = self._handle.readline()
            self.saveline(line)
        return line

    def tell(self):
        """Return the current position of the file read/write pointer within the File."""
        return self._handle.tell() - sum(len(line) for line in self._saved)

    def seek(self, *args):
        """Set the current position at the offset specified."""
        self._saved = []
        self._handle.seek(*args)

    def __getattr__(self, attr):
        """Return File attribute."""
        return getattr(self._handle, attr)

    def __enter__(self):
        """Call special method when opening the file using a with-statement."""
        return self

    def __exit__(self, type, value, traceback):
        """Call special method when closing the file using a with-statement."""
        self._handle.close()


# The rest of this file defines code used in Bio.SeqIO and Bio.SearchIO
# for indexing

class _IndexedSeqFileProxy(object):
    """Base class for file format specific random access (PRIVATE).

    This is subclasses in both Bio.SeqIO for indexing as SeqRecord
    objects, and in Bio.SearchIO for indexing QueryResult objects.

    Subclasses for each file format should define '__iter__', 'get'
    and optionally 'get_raw' methods.
    """

    def __iter__(self):
        """Return (identifier, offset, length in bytes) tuples.

        The length can be zero where it is not implemented or not
        possible for a particular file format.
        """
        raise NotImplementedError("Subclass should implement this")

    def get(self, offset):
        """Return parsed object for this entry."""
        # Most file formats with self contained records can be handled by
        # parsing StringIO(_bytes_to_string(self.get_raw(offset)))
        raise NotImplementedError("Subclass should implement this")

    def get_raw(self, offset):
        """Return the raw record from the file as a bytes string (if implemented).

        If the key is not found, a KeyError exception is raised.

        This may not have been implemented for all file formats.
        """
        # Should be done by each sub-class (if possible)
        raise NotImplementedError("Not available for this file format.")


class _IndexedSeqFileDict(_dict_base):
    """Read only dictionary interface to a sequential record file.

    This code is used in both Bio.SeqIO for indexing as SeqRecord
    objects, and in Bio.SearchIO for indexing QueryResult objects.

    Keeps the keys and associated file offsets in memory, reads the file
    to access entries as objects parsing them on demand. This approach
    is memory limited, but will work even with millions of records.

    Note duplicate keys are not allowed. If this happens, a ValueError
    exception is raised.

    As used in Bio.SeqIO, by default the SeqRecord's id string is used
    as the dictionary key. In Bio.SearchIO, the query's id string is
    used. This can be changed by supplying an optional key_function,
    a callback function which will be given the record id and must
    return the desired key. For example, this allows you to parse
    NCBI style FASTA identifiers, and extract the GI number to use
    as the dictionary key.

    Note that this dictionary is essentially read only. You cannot
    add or change values, pop values, nor clear the dictionary.
    """

    def __init__(self, random_access_proxy, key_function,
                 repr, obj_repr):
        """Initialize the class."""
        # Use key_function=None for default value
        self._proxy = random_access_proxy
        self._key_function = key_function
        self._repr = repr
        self._obj_repr = obj_repr
        if key_function:
            offset_iter = (
                (key_function(k), o, l) for (k, o, l) in random_access_proxy)
        else:
            offset_iter = random_access_proxy
        offsets = _dict()
        for key, offset, length in offset_iter:
            # Note - we don't store the length because I want to minimise the
            # memory requirements. With the SQLite backend the length is kept
            # and is used to speed up the get_raw method (by about 3 times).
            # The length should be provided by all the current backends except
            # SFF where there is an existing Roche index we can reuse (very fast
            # but lacks the record lengths)
            # assert length or format in ["sff", "sff-trim"], \
            #       "%s at offset %i given length %r (%s format %s)" \
            #       % (key, offset, length, filename, format)
            if key in offsets:
                self._proxy._handle.close()
                raise ValueError("Duplicate key '%s'" % key)
            else:
                offsets[key] = offset
        self._offsets = offsets

    def __repr__(self):
        """Return a string representation of the File object."""
        return self._repr

    def __str__(self):
        """Create a string representation of the File object."""
        # TODO - How best to handle the __str__ for SeqIO and SearchIO?
        if self:
            return "{%r : %s(...), ...}" % (list(self.keys())[0], self._obj_repr)
        else:
            return "{}"

    def __contains__(self, key):
        """Return key if contained in the offsets dictionary."""
        return key in self._offsets

    def __len__(self):
        """Return the number of records."""
        return len(self._offsets)

    def items(self):
        """Iterate over the (key, SeqRecord) items.

        This tries to act like a Python 3 dictionary, and does not return
        a list of (key, value) pairs due to memory concerns.
        """
        for key in self.__iter__():
            yield key, self.__getitem__(key)

    def values(self):
        """Iterate over the SeqRecord items.

        This tries to act like a Python 3 dictionary, and does not return
        a list of value due to memory concerns.
        """
        for key in self.__iter__():
            yield self.__getitem__(key)

    def keys(self):
        """Iterate over the keys.

        This tries to act like a Python 3 dictionary, and does not return
        a list of keys due to memory concerns.
        """
        return self.__iter__()

    if hasattr(dict, "iteritems"):
        # Python 2, also define iteritems etc
        def itervalues(self):
            """Iterate over the SeqRecord) items."""
            for key in self.__iter__():
                yield self.__getitem__(key)

        def iteritems(self):
            """Iterate over the (key, SeqRecord) items."""
            for key in self.__iter__():
                yield key, self.__getitem__(key)

        def iterkeys(self):
            """Iterate over the keys."""
            return self.__iter__()

    def __iter__(self):
        """Iterate over the keys."""
        return iter(self._offsets)

    def __getitem__(self, key):
        """Return record for the specified key."""
        # Pass the offset to the proxy
        record = self._proxy.get(self._offsets[key])
        if self._key_function:
            key2 = self._key_function(record.id)
        else:
            key2 = record.id
        if key != key2:
            raise ValueError("Key did not match (%s vs %s)" % (key, key2))
        return record

    def get(self, k, d=None):
        """Return the value in the dictionary.

        If the key (k) is not found, this returns None unless a
        default (d) is specified.
        """
        try:
            return self.__getitem__(k)
        except KeyError:
            return d

    def get_raw(self, key):
        """Return the raw record from the file as a bytes string.

        If the key is not found, a KeyError exception is raised.
        """
        # Pass the offset to the proxy
        return self._proxy.get_raw(self._offsets[key])

    def __setitem__(self, key, value):
        """Would allow setting or replacing records, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def update(self, *args, **kwargs):
        """Would allow adding more values, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def pop(self, key, default=None):
        """Would remove specified record, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def popitem(self):
        """Would remove and return a SeqRecord, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def clear(self):
        """Would clear dictionary, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def fromkeys(self, keys, value=None):
        """Would return a new dictionary with keys and values, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file doesn't "
                                  "support this.")

    def copy(self):
        """Would copy a dictionary, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file doesn't "
                                  "support this.")

    def close(self):
        """Close the file handle being used to read the data.

        Once called, further use of the index won't work. The sole purpose
        of this method is to allow explicit handle closure - for example
        if you wish to delete the file, on Windows you must first close
        all open handles to that file.
        """
        self._proxy._handle.close()


class _SQLiteManySeqFilesDict(_IndexedSeqFileDict):
    """Read only dictionary interface to many sequential record files.

    This code is used in both Bio.SeqIO for indexing as SeqRecord
    objects, and in Bio.SearchIO for indexing QueryResult objects.

    Keeps the keys, file-numbers and offsets in an SQLite database. To access
    a record by key, reads from the offset in the appropriate file and then
    parses the record into an object.

    There are OS limits on the number of files that can be open at once,
    so a pool are kept. If a record is required from a closed file, then
    one of the open handles is closed first.
    """

    def __init__(self, index_filename, filenames,
                 proxy_factory, format,
                 key_function, repr, max_open=10):
        """Initialize the class."""
        # TODO? - Don't keep filename list in memory (just in DB)?
        # Should save a chunk of memory if dealing with 1000s of files.
        # Furthermore could compare a generator to the DB on reloading
        # (no need to turn it into a list)

        if not _sqlite:
            # Hack for Jython (of if Python is compiled without it)
            from Bio import MissingPythonDependencyError
            raise MissingPythonDependencyError("Requires sqlite3, which is "
                                               "included Python 2.5+")
        if filenames is not None:
            filenames = list(filenames)  # In case it was a generator

        # Cache the arguments as private variables
        self._index_filename = index_filename
        self._filenames = filenames
        self._format = format
        self._key_function = key_function
        self._proxy_factory = proxy_factory
        self._repr = repr
        self._max_open = max_open
        self._proxies = {}

        # Note if using SQLite :memory: trick index filename, this will
        # give $PWD as the relative path (which is fine).
        self._relative_path = os.path.abspath(os.path.dirname(index_filename))

        if os.path.isfile(index_filename):
            self._load_index()
        else:
            self._build_index()

    def _load_index(self):
        """Call from __init__ to re-use an existing index (PRIVATE)."""
        index_filename = self._index_filename
        relative_path = self._relative_path
        filenames = self._filenames
        format = self._format
        proxy_factory = self._proxy_factory

        con = _sqlite.connect(index_filename,
                              check_same_thread=False)
        self._con = con
        # Check the count...
        try:
            count, = con.execute(
                "SELECT value FROM meta_data WHERE key=?;",
                ("count",)).fetchone()
            self._length = int(count)
            if self._length == -1:
                con.close()
                raise ValueError("Unfinished/partial database")

            # use MAX(_ROWID_) to obtain the number of sequences in the database
            # using COUNT(key) is quite slow in SQLITE
            # (https://stackoverflow.com/questions/8988915/sqlite-count-slow-on-big-tables)
            count, = con.execute(
                "SELECT MAX(_ROWID_) FROM offset_data;").fetchone()
            if self._length != int(count):
                con.close()
                raise ValueError("Corrupt database? %i entries not %i"
                                 % (int(count), self._length))
            self._format, = con.execute(
                "SELECT value FROM meta_data WHERE key=?;",
                ("format",)).fetchone()
            if format and format != self._format:
                con.close()
                raise ValueError("Index file says format %s, not %s"
                                 % (self._format, format))
            try:
                filenames_relative_to_index, = con.execute(
                    "SELECT value FROM meta_data WHERE key=?;",
                    ("filenames_relative_to_index",)).fetchone()
                filenames_relative_to_index = (filenames_relative_to_index.upper() == "TRUE")
            except TypeError:
                # Original behaviour, assume if meta_data missing
                filenames_relative_to_index = False
            self._filenames = [row[0] for row in
                               con.execute("SELECT name FROM file_data "
                                           "ORDER BY file_number;").fetchall()]
            if filenames_relative_to_index:
                # Not implicitly relative to $PWD, explicitly relative to index file
                relative_path = os.path.abspath(os.path.dirname(index_filename))
                tmp = []
                for f in self._filenames:
                    if os.path.isabs(f):
                        tmp.append(f)
                    else:
                        # Would be stored with Unix / path separator, so convert
                        # it to the local OS path separator here:
                        tmp.append(os.path.join(relative_path, f.replace("/", os.path.sep)))
                self._filenames = tmp
                del tmp
            if filenames and len(filenames) != len(self._filenames):
                con.close()
                raise ValueError("Index file says %i files, not %i"
                                 % (len(self._filenames), len(filenames)))
            if filenames and filenames != self._filenames:
                for old, new in zip(self._filenames, filenames):
                    # Want exact match (after making relative to the index above)
                    if os.path.abspath(old) != os.path.abspath(new):
                        con.close()
                        if filenames_relative_to_index:
                            raise ValueError("Index file has different filenames, e.g. %r != %r"
                                             % (os.path.abspath(old), os.path.abspath(new)))
                        else:
                            raise ValueError("Index file has different filenames "
                                             "[This is an old index where any relative paths "
                                             "were relative to the original working directory]. "
                                             "e.g. %r != %r"
                                             % (os.path.abspath(old), os.path.abspath(new)))
                # Filenames are equal (after imposing abspath)
        except _OperationalError as err:
            con.close()
            raise ValueError("Not a Biopython index database? %s" % err)
        # Now we have the format (from the DB if not given to us),
        if not proxy_factory(self._format):
            con.close()
            raise ValueError("Unsupported format '%s'" % self._format)

    def _build_index(self):
        """Call from __init__ to create a new index (PRIVATE)."""
        index_filename = self._index_filename
        relative_path = self._relative_path
        filenames = self._filenames
        format = self._format
        key_function = self._key_function
        proxy_factory = self._proxy_factory
        max_open = self._max_open
        random_access_proxies = self._proxies

        if not format or not filenames:
            raise ValueError("Filenames to index and format required to build %r" % index_filename)
        if not proxy_factory(format):
            raise ValueError("Unsupported format '%s'" % format)
        # Create the index
        con = _sqlite.connect(index_filename)
        self._con = con
        # print("Creating index")
        # Sqlite PRAGMA settings for speed
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA locking_mode=EXCLUSIVE")
        # Don't index the key column until the end (faster)
        # con.execute("CREATE TABLE offset_data (key TEXT PRIMARY KEY, "
        #             "offset INTEGER);")
        con.execute("CREATE TABLE meta_data (key TEXT, value TEXT);")
        con.execute("INSERT INTO meta_data (key, value) VALUES (?,?);",
                    ("count", -1))
        con.execute("INSERT INTO meta_data (key, value) VALUES (?,?);",
                    ("format", format))
        con.execute("INSERT INTO meta_data (key, value) VALUES (?,?);",
                    ("filenames_relative_to_index", "True"))
        # TODO - Record the alphabet?
        # TODO - Record the file size and modified date?
        con.execute(
            "CREATE TABLE file_data (file_number INTEGER, name TEXT);")
        con.execute("CREATE TABLE offset_data (key TEXT, "
                    "file_number INTEGER, offset INTEGER, length INTEGER);")
        count = 0
        for i, filename in enumerate(filenames):
            # Default to storing as an absolute path,
            f = os.path.abspath(filename)
            if not os.path.isabs(filename) and not os.path.isabs(index_filename):
                # Since user gave BOTH filename & index as relative paths,
                # we will store this relative to the index file even though
                # if it may now start ../ (meaning up a level)
                # Note for cross platform use (e.g. shared drive over SAMBA),
                # convert any Windows slash into Unix style for rel paths.
                f = os.path.relpath(filename, relative_path).replace(os.path.sep, "/")
            elif (os.path.dirname(os.path.abspath(filename)) +
                  os.path.sep).startswith(relative_path + os.path.sep):
                # Since sequence file is in same directory or sub directory,
                # might as well make this into a relative path:
                f = os.path.relpath(filename, relative_path).replace(os.path.sep, "/")
                assert not f.startswith("../"), f
            # print("DEBUG - storing %r as [%r] %r" % (filename, relative_path, f))
            con.execute(
                "INSERT INTO file_data (file_number, name) VALUES (?,?);",
                (i, f))
            random_access_proxy = proxy_factory(format, filename)
            if key_function:
                offset_iter = ((key_function(k), i, o, l)
                               for (k, o, l) in random_access_proxy)
            else:
                offset_iter = ((k, i, o, l)
                               for (k, o, l) in random_access_proxy)
            while True:
                batch = list(itertools.islice(offset_iter, 100))
                if not batch:
                    break
                # print("Inserting batch of %i offsets, %s ... %s"
                #       % (len(batch), batch[0][0], batch[-1][0]))
                con.executemany(
                    "INSERT INTO offset_data (key,file_number,offset,length) VALUES (?,?,?,?);",
                    batch)
                con.commit()
                count += len(batch)
            if len(random_access_proxies) < max_open:
                random_access_proxies[i] = random_access_proxy
            else:
                random_access_proxy._handle.close()
        self._length = count
        # print("About to index %i entries" % count)
        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                        "key_index ON offset_data(key);")
        except _IntegrityError as err:
            self._proxies = random_access_proxies
            self.close()
            con.close()
            raise ValueError("Duplicate key? %s" % err)
        con.execute("PRAGMA locking_mode=NORMAL")
        con.execute("UPDATE meta_data SET value = ? WHERE key = ?;",
                    (count, "count"))
        con.commit()
        # print("Index created")

    def __repr__(self):
        return self._repr

    def __contains__(self, key):
        return bool(
            self._con.execute("SELECT key FROM offset_data WHERE key=?;",
                              (key,)).fetchone())

    def __len__(self):
        """Return the number of records indexed."""
        return self._length
        # return self._con.execute("SELECT COUNT(key) FROM offset_data;").fetchone()[0]

    def __iter__(self):
        """Iterate over the keys."""
        for row in self._con.execute("SELECT key FROM offset_data ORDER BY file_number, offset;"):
            yield str(row[0])

    if hasattr(dict, "iteritems"):
        # Python 2, use iteritems but not items etc
        # Just need to override this...
        def keys(self):
            """Iterate over the keys.

            This tries to act like a Python 3 dictionary, and does not return
            a list of keys due to memory concerns.
            """
            return [str(row[0]) for row in
                    self._con.execute("SELECT key FROM offset_data;").fetchall()]

    def __getitem__(self, key):
        """Return record for the specified key."""
        # Pass the offset to the proxy
        row = self._con.execute(
            "SELECT file_number, offset FROM offset_data WHERE key=?;",
            (key,)).fetchone()
        if not row:
            raise KeyError
        file_number, offset = row
        proxies = self._proxies
        if file_number in proxies:
            record = proxies[file_number].get(offset)
        else:
            if len(proxies) >= self._max_open:
                # Close an old handle...
                proxies.popitem()[1]._handle.close()
            # Open a new handle...
            proxy = self._proxy_factory(self._format, self._filenames[file_number])
            record = proxy.get(offset)
            proxies[file_number] = proxy
        if self._key_function:
            key2 = self._key_function(record.id)
        else:
            key2 = record.id
        if key != key2:
            raise ValueError("Key did not match (%s vs %s)" % (key, key2))
        return record

    def get(self, k, d=None):
        """Return the value in the dictionary.

        If the key (k) is not found, this returns None unless a
        default (d) is specified.
        """
        try:
            return self.__getitem__(k)
        except KeyError:
            return d

    def get_raw(self, key):
        """Return the raw record from the file as a bytes string.

        If the key is not found, a KeyError exception is raised.
        """
        # Pass the offset to the proxy
        row = self._con.execute(
            "SELECT file_number, offset, length FROM offset_data WHERE key=?;",
            (key,)).fetchone()
        if not row:
            raise KeyError
        file_number, offset, length = row
        proxies = self._proxies
        if file_number in proxies:
            if length:
                # Shortcut if we have the length
                h = proxies[file_number]._handle
                h.seek(offset)
                return h.read(length)
            else:
                return proxies[file_number].get_raw(offset)
        else:
            # This code is duplicated from __getitem__ to avoid a function call
            if len(proxies) >= self._max_open:
                # Close an old handle...
                proxies.popitem()[1]._handle.close()
            # Open a new handle...
            proxy = self._proxy_factory(self._format, self._filenames[file_number])
            proxies[file_number] = proxy
            if length:
                # Shortcut if we have the length
                h = proxy._handle
                h.seek(offset)
                return h.read(length)
            else:
                return proxy.get_raw(offset)

    def close(self):
        """Close any open file handles."""
        proxies = self._proxies
        while proxies:
            proxies.popitem()[1]._handle.close()

class _IndexedSeqFileDictORF(_dict_base):

    """Edited by Murat Buyukyoruk"""

    def __init__(self, random_access_proxy, key_function,
                 repr, obj_repr):
        """Initialize the class."""
        # Use key_function=None for default value
        self._proxy = random_access_proxy
        self._key_function = key_function
        self._repr = repr
        self._obj_repr = obj_repr
        if key_function:
            offset_iter = (
                (key_function(k), o, l) for (k, o, l) in random_access_proxy)
        else:
            offset_iter = random_access_proxy
        offsets = _dict()
        for key, offset, length in offset_iter:
            # Note - we don't store the length because I want to minimise the
            # memory requirements. With the SQLite backend the length is kept
            # and is used to speed up the get_raw method (by about 3 times).
            # The length should be provided by all the current backends except
            # SFF where there is an existing Roche index we can reuse (very fast
            # but lacks the record lengths)
            # assert length or format in ["sff", "sff-trim"], \
            #       "%s at offset %i given length %r (%s format %s)" \
            #       % (key, offset, length, filename, format)

            if key in offsets:
                if isinstance(offsets[key], list):
                    offsets[key].append(offset)
                else:
                    offsets[key] = [offsets[key]]
                    offsets[key].append(offset)
            else:
                offsets[key] = offset
        self._offsets = offsets

    def __repr__(self):
        """Return a string representation of the File object."""
        return self._repr

    def __str__(self):
        """Create a string representation of the File object."""
        # TODO - How best to handle the __str__ for SeqIO and SearchIO?
        if self:
            return "{%r : %s(...), ...}" % (list(self.keys())[0], self._obj_repr)
        else:
            return "{}"

    def __contains__(self, key):
        """Return key if contained in the offsets dictionary."""
        return key in self._offsets

    def __len__(self):
        """Return the number of records."""
        return len(self._offsets)

    def items(self):
        """Iterate over the (key, SeqRecord) items.

        This tries to act like a Python 3 dictionary, and does not return
        a list of (key, value) pairs due to memory concerns.
        """
        for key in self.__iter__():
            yield key, self.__getitem__(key)

    def values(self):
        """Iterate over the SeqRecord items.

        This tries to act like a Python 3 dictionary, and does not return
        a list of value due to memory concerns.
        """
        for key in self.__iter__():
            yield self.__getitem__(key)

    def keys(self):
        """Iterate over the keys.

        This tries to act like a Python 3 dictionary, and does not return
        a list of keys due to memory concerns.
        """
        return self.__iter__()

    if hasattr(dict, "iteritems"):
        # Python 2, also define iteritems etc
        def itervalues(self):
            """Iterate over the SeqRecord) items."""
            for key in self.__iter__():
                yield self.__getitem__(key)

        def iteritems(self):
            """Iterate over the (key, SeqRecord) items."""
            for key in self.__iter__():
                yield key, self.__getitem__(key)

        def iterkeys(self):
            """Iterate over the keys."""
            return self.__iter__()

    def __iter__(self):
        """Iterate over the keys."""
        return iter(self._offsets)

    def __getitem__(self, key):
        """Return record for the specified key."""
        # Pass the offset to the proxy
        if isinstance(self._offsets[key], list):
            record_list = []
            for i in range(len(self._offsets[key])):
                record = self._proxy.get(self._offsets[key][i])
                if self._key_function:
                    key2 = self._key_function(record.id)
                else:
                    key2 = record.id
                if key != key2:
                    raise ValueError("Key did not match (%s vs %s)" % (key, key2))
                record_list.append(record)
            record = record_list
            return record
        else:
            record = self._proxy.get(self._offsets[key])
            if self._key_function:
                key2 = self._key_function(record.id)
            else:
                key2 = record.id
            if key != key2:
                raise ValueError("Key did not match (%s vs %s)" % (key, key2))
            return record

    def get(self, k, d=None):
        """Return the value in the dictionary.

        If the key (k) is not found, this returns None unless a
        default (d) is specified.
        """
        try:
            return self.__getitem__(k)
        except KeyError:
            return d

    def get_raw(self, key):
        """Return the raw record from the file as a bytes string.

        If the key is not found, a KeyError exception is raised.
        """
        # Pass the offset to the proxy
        return self._proxy.get_raw(self._offsets[key])

    def __setitem__(self, key, value):
        """Would allow setting or replacing records, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def update(self, *args, **kwargs):
        """Would allow adding more values, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def pop(self, key, default=None):
        """Would remove specified record, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def popitem(self):
        """Would remove and return a SeqRecord, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def clear(self):
        """Would clear dictionary, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file is read only.")

    def fromkeys(self, keys, value=None):
        """Would return a new dictionary with keys and values, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file doesn't "
                                  "support this.")

    def copy(self):
        """Would copy a dictionary, but not implemented.

        Python dictionaries provide this method for modifying data in the
        dictionary. This class mimics the dictionary interface but is read only.
        """
        raise NotImplementedError("An indexed a sequence file doesn't "
                                  "support this.")

    def close(self):
        """Close the file handle being used to read the data.

        Once called, further use of the index won't work. The sole purpose
        of this method is to allow explicit handle closure - for example
        if you wish to delete the file, on Windows you must first close
        all open handles to that file.
        """
        self._proxy._handle.close()


class _SQLiteManySeqFilesDictORF(_IndexedSeqFileDict):

    """Edited by Murat Buyukyoruk"""

    def __init__(self, index_filename, filenames,
                 proxy_factory, format,
                 key_function, repr, max_open=10):
        """Initialize the class."""
        # TODO? - Don't keep filename list in memory (just in DB)?
        # Should save a chunk of memory if dealing with 1000s of files.
        # Furthermore could compare a generator to the DB on reloading
        # (no need to turn it into a list)

        if not _sqlite:
            # Hack for Jython (of if Python is compiled without it)
            from Bio import MissingPythonDependencyError
            raise MissingPythonDependencyError("Requires sqlite3, which is "
                                               "included Python 2.5+")
        if filenames is not None:
            filenames = list(filenames)  # In case it was a generator

        # Cache the arguments as private variables
        self._index_filename = index_filename
        self._filenames = filenames
        self._format = format
        self._key_function = key_function
        self._proxy_factory = proxy_factory
        self._repr = repr
        self._max_open = max_open
        self._proxies = {}

        # Note if using SQLite :memory: trick index filename, this will
        # give $PWD as the relative path (which is fine).
        self._relative_path = os.path.abspath(os.path.dirname(index_filename))

        if os.path.isfile(index_filename):
            self._load_index()
        else:
            self._build_index()

    def _load_index(self):
        """Call from __init__ to re-use an existing index (PRIVATE)."""
        index_filename = self._index_filename
        relative_path = self._relative_path
        filenames = self._filenames
        format = self._format
        proxy_factory = self._proxy_factory

        con = _sqlite.connect(index_filename,
                              check_same_thread=False)
        self._con = con
        # Check the count...
        try:
            count, = con.execute(
                "SELECT value FROM meta_data WHERE key=?;",
                ("count",)).fetchone()

            self._length = int(count)
            if self._length == -1:
                con.close()
                raise ValueError("Unfinished/partial database")

            # use MAX(_ROWID_) to obtain the number of sequences in the database
            # using COUNT(key) is quite slow in SQLITE
            # (https://stackoverflow.com/questions/8988915/sqlite-count-slow-on-big-tables)
            count, = con.execute(
                "SELECT MAX(_ROWID_) FROM offset_data;").fetchone()
            if self._length != int(count):
                con.close()
                raise ValueError("Corrupt database? %i entries not %i"
                                 % (int(count), self._length))
            self._format, = con.execute(
                "SELECT value FROM meta_data WHERE key=?;",
                ("format",)).fetchone()
            if format and format != self._format:
                con.close()
                raise ValueError("Index file says format %s, not %s"
                                 % (self._format, format))
            try:
                filenames_relative_to_index, = con.execute(
                    "SELECT value FROM meta_data WHERE key=?;",
                    ("filenames_relative_to_index",)).fetchone()
                filenames_relative_to_index = (filenames_relative_to_index.upper() == "TRUE")
            except TypeError:
                # Original behaviour, assume if meta_data missing
                filenames_relative_to_index = False
            self._filenames = [row[0] for row in
                               con.execute("SELECT name FROM file_data "
                                           "ORDER BY file_number;").fetchall()]
            if filenames_relative_to_index:
                # Not implicitly relative to $PWD, explicitly relative to index file
                relative_path = os.path.abspath(os.path.dirname(index_filename))
                tmp = []
                for f in self._filenames:
                    if os.path.isabs(f):
                        tmp.append(f)
                    else:
                        # Would be stored with Unix / path separator, so convert
                        # it to the local OS path separator here:
                        tmp.append(os.path.join(relative_path, f.replace("/", os.path.sep)))
                self._filenames = tmp
                del tmp
            if filenames and len(filenames) != len(self._filenames):
                con.close()
                raise ValueError("Index file says %i files, not %i"
                                 % (len(self._filenames), len(filenames)))
            if filenames and filenames != self._filenames:
                for old, new in zip(self._filenames, filenames):
                    # Want exact match (after making relative to the index above)
                    if os.path.abspath(old) != os.path.abspath(new):
                        con.close()
                        if filenames_relative_to_index:
                            raise ValueError("Index file has different filenames, e.g. %r != %r"
                                             % (os.path.abspath(old), os.path.abspath(new)))
                        else:
                            raise ValueError("Index file has different filenames "
                                             "[This is an old index where any relative paths "
                                             "were relative to the original working directory]. "
                                             "e.g. %r != %r"
                                             % (os.path.abspath(old), os.path.abspath(new)))
                # Filenames are equal (after imposing abspath)
        except _OperationalError as err:
            con.close()
            raise ValueError("Not a Biopython index database? %s" % err)
        # Now we have the format (from the DB if not given to us),
        if not proxy_factory(self._format):
            con.close()
            raise ValueError("Unsupported format '%s'" % self._format)

    def _build_index(self):
        """Call from __init__ to create a new index (PRIVATE)."""
        index_filename = self._index_filename
        relative_path = self._relative_path
        filenames = self._filenames
        format = self._format
        key_function = self._key_function
        proxy_factory = self._proxy_factory
        max_open = self._max_open
        random_access_proxies = self._proxies

        if not format or not filenames:
            raise ValueError("Filenames to index and format required to build %r" % index_filename)
        if not proxy_factory(format):
            raise ValueError("Unsupported format '%s'" % format)
        # Create the index
        con = _sqlite.connect(index_filename)
        self._con = con
        # print("Creating index")
        # Sqlite PRAGMA settings for speed
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA locking_mode=EXCLUSIVE")
        # Don't index the key column until the end (faster)
        # con.execute("CREATE TABLE offset_data (key TEXT PRIMARY KEY, "
        #             "offset INTEGER);")
        con.execute("CREATE TABLE meta_data (key TEXT, value TEXT);")
        con.execute("INSERT INTO meta_data (key, value) VALUES (?,?);",
                    ("count", -1))
        con.execute("INSERT INTO meta_data (key, value) VALUES (?,?);",
                    ("format", format))
        con.execute("INSERT INTO meta_data (key, value) VALUES (?,?);",
                    ("filenames_relative_to_index", "True"))
        # TODO - Record the alphabet?
        # TODO - Record the file size and modified date?
        con.execute(
            "CREATE TABLE file_data (file_number INTEGER, name TEXT);")
        con.execute("CREATE TABLE offset_data (key TEXT, "
                    "file_number INTEGER, offset INTEGER, length INTEGER);")
        count = 0
        for i, filename in enumerate(filenames):
            # Default to storing as an absolute path,
            f = os.path.abspath(filename)
            if not os.path.isabs(filename) and not os.path.isabs(index_filename):
                # Since user gave BOTH filename & index as relative paths,
                # we will store this relative to the index file even though
                # if it may now start ../ (meaning up a level)
                # Note for cross platform use (e.g. shared drive over SAMBA),
                # convert any Windows slash into Unix style for rel paths.
                f = os.path.relpath(filename, relative_path).replace(os.path.sep, "/")
            elif (os.path.dirname(os.path.abspath(filename)) +
                  os.path.sep).startswith(relative_path + os.path.sep):
                # Since sequence file is in same directory or sub directory,
                # might as well make this into a relative path:
                f = os.path.relpath(filename, relative_path).replace(os.path.sep, "/")
                assert not f.startswith("../"), f
            # print("DEBUG - storing %r as [%r] %r" % (filename, relative_path, f))
            con.execute(
                "INSERT INTO file_data (file_number, name) VALUES (?,?);",
                (i, f))
            random_access_proxy = proxy_factory(format, filename)
            if key_function:
                offset_iter = ((key_function(k), i, o, l)
                               for (k, o, l) in random_access_proxy)
            else:
                offset_iter = ((k, i, o, l)
                               for (k, o, l) in random_access_proxy)
            while True:
                batch = list(itertools.islice(offset_iter, 100))
                if not batch:
                    break
                # print("Inserting batch of %i offsets, %s ... %s"
                #       % (len(batch), batch[0][0], batch[-1][0]))
                con.executemany(
                    "INSERT INTO offset_data (key,file_number,offset,length) VALUES (?,?,?,?);",
                    batch)
                con.commit()
                count += len(batch)
            if len(random_access_proxies) < max_open:
                random_access_proxies[i] = random_access_proxy
            else:
                random_access_proxy._handle.close()
        self._length = count
        # print("About to index %i entries" % count)
        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                        "key_index ON offset_data(key);")
            con.execute("PRAGMA locking_mode=NORMAL")
            con.execute("UPDATE meta_data SET value = ? WHERE key = ?;",
                        (count, "count"))
        except _IntegrityError as err:
            con.execute("""
            CREATE TABLE temp_offset_data AS
            SELECT
            key,
            MIN(file_number) AS file_number,
            GROUP_CONCAT(offset) AS offset,
            GROUP_CONCAT(length) AS length
            FROM offset_data
            GROUP BY key;
            """)
            con.execute("DROP TABLE offset_data;")
            con.execute("ALTER TABLE temp_offset_data RENAME TO offset_data;")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                        "key_index ON offset_data(key);")
            row_count = self._con.execute("SELECT COUNT(*) FROM offset_data;").fetchone()[0]

            con.execute("PRAGMA locking_mode=NORMAL")
            con.execute("UPDATE meta_data SET value = ? WHERE key = ?;",
                        (row_count, "count"))
        con.commit()
        # print("Index created")
    def __repr__(self):
        return self._repr

    def __contains__(self, key):
        return bool(
            self._con.execute("SELECT key FROM offset_data WHERE key=?;",
                              (key,)).fetchone())

    def __len__(self):
        """Return the number of records indexed."""
        return self._length
        # return self._con.execute("SELECT COUNT(key) FROM offset_data;").fetchone()[0]

    def __iter__(self):
        """Iterate over the keys."""
        for row in self._con.execute("SELECT key FROM offset_data ORDER BY file_number, offset;"):
            yield str(row[0])

    if hasattr(dict, "iteritems"):
        # Python 2, use iteritems but not items etc
        # Just need to override this...
        def keys(self):
            """Iterate over the keys.

            This tries to act like a Python 3 dictionary, and does not return
            a list of keys due to memory concerns.
            """
            return [str(row[0]) for row in
                    self._con.execute("SELECT key FROM offset_data;").fetchall()]

    def __getitem__(self, key):
        """Return record for the specified key."""
        # Pass the offset to the proxy
        row = self._con.execute(
            "SELECT file_number, offset FROM offset_data WHERE key=?;",
            (key,)).fetchone()

        if not row:
            raise KeyError

        file_number = row[0]
        try:
            offset_ask = row[1].split(",")
        except:
            offset_ask = row[1]
        if isinstance(offset_ask, list):
            record_list = []
            for i in range(len(offset_ask)):
                offset = int(offset_ask[i])
                proxies = self._proxies
                if file_number in proxies:
                    record = proxies[file_number].get(offset)
                else:
                    if len(proxies) >= self._max_open:
                    # Close an old handle...
                        proxies.popitem()[1]._handle.close()
                    # Open a new handle...
                    proxy = self._proxy_factory(self._format, self._filenames[file_number])
                    record = proxy.get(offset)
                    proxies[file_number] = proxy
                if self._key_function:
                    key2 = self._key_function(record.id)
                else:
                    key2 = record.id
                if key != key2:
                    raise ValueError("Key did not match (%s vs %s)" % (key, key2))
                record_list.append(record)
            record = record_list
            return record
        else:
            offset = int(row[1])
            proxies = self._proxies
            if file_number in proxies:
                record = proxies[file_number].get(offset)
            else:
                if len(proxies) >= self._max_open:
                    # Close an old handle...
                    proxies.popitem()[1]._handle.close()
                # Open a new handle...
                proxy = self._proxy_factory(self._format, self._filenames[file_number])
                record = proxy.get(offset)
                proxies[file_number] = proxy
            if self._key_function:
                key2 = self._key_function(record.id)
            else:
                key2 = record.id
            if key != key2:
                raise ValueError("Key did not match (%s vs %s)" % (key, key2))
            return record

    def get(self, k, d=None):
        """Return the value in the dictionary.

        If the key (k) is not found, this returns None unless a
        default (d) is specified.
        """
        try:
            return self.__getitem__(k)
        except KeyError:
            return d

    def get_raw(self, key):
        """Return the raw record from the file as a bytes string.

        If the key is not found, a KeyError exception is raised.
        """
        # Pass the offset to the proxy
        row = self._con.execute(
            "SELECT file_number, offset, length FROM offset_data WHERE key=?;",
            (key,)).fetchone()
        if not row:
            raise KeyError
        file_number, offset, length = row
        proxies = self._proxies
        if file_number in proxies:
            if length:
                # Shortcut if we have the length
                h = proxies[file_number]._handle
                h.seek(offset)
                return h.read(length)
            else:
                return proxies[file_number].get_raw(offset)
        else:
            # This code is duplicated from __getitem__ to avoid a function call
            if len(proxies) >= self._max_open:
                # Close an old handle...
                proxies.popitem()[1]._handle.close()
            # Open a new handle...
            proxy = self._proxy_factory(self._format, self._filenames[file_number])
            proxies[file_number] = proxy
            if length:
                # Shortcut if we have the length
                h = proxy._handle
                h.seek(offset)
                return h.read(length)
            else:
                return proxy.get_raw(offset)

    def close(self):
        """Close any open file handles."""
        proxies = self._proxies
        while proxies:
            proxies.popitem()[1]._handle.close()

